"""Replay Strategy 5 signal generation against today's historical candle data.

Walks minute-by-minute through the trading day for each watchlist symbol,
building MarketContext from DB candles and calling strategy.evaluate().

Usage:
    cd backend && source .venv/bin/activate
    python scripts/replay_strategy5.py [--date 2026-04-28] [--symbols VEDL,TATAPOWER]

Requires: candle data in market_data_1m for both the target date and its
previous trading day. Skips RVOL filter (no baseline profiles needed).
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
from app.core.redis import get_redis
from app.indicators.atr import compute_atr
from app.indicators.candle_patterns import Candle
from app.indicators.cpr import calculate_cpr
from app.indicators.previous_day import PreviousDayLevels
from app.indicators.vwap import calculate_vwap
from app.services.strategy_params import INTRADAY_FUTURES_DEFAULTS
from app.strategies.base import MarketContext, StrategySignal
from app.strategies.strategy_5_intraday_futures import (
    IntradayFuturesStrategy,
    get_current_phase,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB helpers (reused from context_builder, trimmed for Strategy 5 needs)
# ---------------------------------------------------------------------------

async def fetch_candles_1m(session, symbol: str, from_ts: datetime, to_ts: datetime) -> list[Candle]:
    from sqlalchemy import text

    result = await session.execute(
        text("""
            SELECT DISTINCT ON (date_trunc('minute', timestamp))
                open, high, low, close, volume
            FROM market_data_1m
            WHERE symbol = :symbol
              AND timestamp BETWEEN :from_ts AND :to_ts
            ORDER BY date_trunc('minute', timestamp), volume DESC, timestamp
        """),
        {"symbol": symbol, "from_ts": from_ts, "to_ts": to_ts},
    )
    return [
        Candle(
            open=float(r.open), high=float(r.high),
            low=float(r.low), close=float(r.close),
            volume=int(r.volume or 0),
        )
        for r in result.all()
    ]


async def fetch_daily_candles(session, symbol: str, days: int = 30) -> list[Candle]:
    from sqlalchemy import text

    # Daily bars live in market_data_daily (since the 2026-05-01 migration).
    # This previously read midnight-UTC rows co-mingled in market_data_1m.
    result = await session.execute(
        text("""
            SELECT open, high, low, close, volume
            FROM market_data_daily
            WHERE symbol = :symbol
            ORDER BY date DESC
            LIMIT :days
        """),
        {"symbol": symbol, "days": days},
    )
    rows = result.all()
    return [
        Candle(
            open=float(r.open), high=float(r.high),
            low=float(r.low), close=float(r.close),
            volume=int(r.volume or 0),
        )
        for r in reversed(rows)
    ]


async def get_previous_day_levels(session, symbol: str, today: date) -> PreviousDayLevels | None:
    from app.services.candle_backfill import _previous_trading_day

    prev_day = _previous_trading_day(today)
    prev_start = datetime.combine(prev_day, MARKET_OPEN, tzinfo=IST)
    prev_end = datetime.combine(prev_day, MARKET_CLOSE, tzinfo=IST)

    candles = await fetch_candles_1m(session, symbol, prev_start, prev_end)
    if not candles:
        return None

    from app.indicators.previous_day import analyze_previous_day

    return analyze_previous_day(
        open_price=candles[0].open,
        high=max(c.high for c in candles),
        low=min(c.low for c in candles),
        close=candles[-1].close,
    )


def aggregate_to_5m(candles_1m: list[Candle]) -> list[Candle]:
    bars: list[Candle] = []
    group: list[Candle] = []
    for i, c in enumerate(candles_1m):
        group.append(c)
        if len(group) == 5 or i == len(candles_1m) - 1:
            bars.append(Candle(
                open=group[0].open,
                high=max(g.high for g in group),
                low=min(g.low for g in group),
                close=group[-1].close,
                volume=sum(g.volume for g in group),
            ))
            group = []
    return bars


def build_vwap(candles_1m: list[Candle]):
    if not candles_1m:
        return None
    return calculate_vwap(
        [c.high for c in candles_1m],
        [c.low for c in candles_1m],
        [c.close for c in candles_1m],
        [c.volume for c in candles_1m],
    )


# ---------------------------------------------------------------------------
# Replay engine
# ---------------------------------------------------------------------------

async def replay_symbol(
    session,
    strategy: IntradayFuturesStrategy,
    symbol: str,
    target_date: date,
    prev_day_levels: PreviousDayLevels | None,
    daily_candles: list[Candle],
    params: dict,
) -> list[dict]:
    """Walk minute-by-minute through the day, evaluate strategy at each 5m boundary."""

    day_start = datetime.combine(target_date, MARKET_OPEN, tzinfo=IST)
    day_end = datetime.combine(target_date, MARKET_CLOSE, tzinfo=IST)

    all_1m = await fetch_candles_1m(session, symbol, day_start, day_end)
    if not all_1m:
        logger.warning(f"{symbol}: no 1m candles for {target_date}")
        return []

    today_open = all_1m[0].open
    volume_avg_20d = None
    if daily_candles:
        recent = daily_candles[-20:]
        volume_avg_20d = int(sum(c.volume for c in recent) / len(recent)) if recent else None

    signals: list[dict] = []
    skip_reasons: dict[str, int] = {}
    last_eval_bar_count = 0
    # Dedup: track fired signals per setup+direction to mimic strategy_runner
    fired_setups: set[tuple[str, str]] = set()

    for minute_idx in range(len(all_1m)):
        candles_so_far = all_1m[: minute_idx + 1]
        candles_5m = aggregate_to_5m(candles_so_far)

        # Only evaluate on new 5m bar completion (every 5 minutes)
        if len(candles_5m) == last_eval_bar_count:
            continue
        last_eval_bar_count = len(candles_5m)

        if len(candles_5m) < 3:
            continue

        current_price = candles_so_far[-1].close
        vwap_result = build_vwap(candles_so_far)
        atr_5m = compute_atr(candles_5m) if len(candles_5m) >= 2 else None

        minutes_from_open = minute_idx
        simulated_time = day_start + timedelta(minutes=minutes_from_open)

        ctx = MarketContext(
            symbol=symbol,
            current_price=current_price,
            candles_5m=candles_5m,
            vwap=vwap_result,
            previous_day=prev_day_levels,
            cpr=calculate_cpr(
                prev_day_levels.pdh, prev_day_levels.pdl, prev_day_levels.pdc
            ) if prev_day_levels else None,
            oi_analysis=None,
            india_vix=None,
            current_time_ist=simulated_time.isoformat(),
            candles_daily=daily_candles if daily_candles else None,
            volume_avg_20d=volume_avg_20d,
            strategy_params=params,
            atr_5m=atr_5m,
            today_open=today_open,
        )

        # Patch now_ist at the source — all local imports read from app.core.utils
        with patch("app.core.utils.now_ist", return_value=simulated_time):
            signal = strategy.evaluate(ctx)

        # Collect skip reasons from pending logs
        for category, msg in strategy.drain_pending_logs():
            if category == "SKIP":
                reason_key = msg.split(": ", 1)[-1] if ": " in msg else msg
                # Collapse numeric values to just the filter name
                for prefix in ("ADR", "RVOL", "vol ratio", "price", "PDH", "PDL"):
                    if reason_key.startswith(prefix):
                        reason_key = prefix + " filter"
                        break
                skip_reasons[reason_key] = skip_reasons.get(reason_key, 0) + 1
            elif category == "PHASE":
                logger.info(f"  [{category}] {msg}")

        if signal is not None:
            setup = signal.indicators.get("setup_type", "?")
            direction = signal.signal_type.value
            dedup_key = (setup, direction)

            if dedup_key in fired_setups:
                continue
            fired_setups.add(dedup_key)

            phase = get_current_phase(simulated_time)
            entry = {
                "symbol": symbol,
                "time": simulated_time.strftime("%H:%M"),
                "phase": phase,
                "signal_type": direction,
                "setup_type": setup,
                "entry_price": round(signal.entry_price, 2),
                "stop_loss": round(signal.stop_loss, 2),
                "target_price": round(signal.target_price, 2) if signal.target_price else None,
                "confidence": signal.confidence,
                "reason": signal.reason,
                "lots": signal.lots,
                "indicators": {
                    k: v for k, v in signal.indicators.items()
                    if k not in ("risk_warnings",)
                },
            }
            signals.append(entry)
            logger.info(
                f"  SIGNAL @ {entry['time']} | {entry['setup_type']} "
                f"{entry['signal_type']} | entry={entry['entry_price']} "
                f"SL={entry['stop_loss']} T={entry['target_price']} "
                f"conf={entry['confidence']}"
            )

    if skip_reasons:
        logger.info(f"  Filters hit: {dict(sorted(skip_reasons.items(), key=lambda x: -x[1]))}")

    return signals


async def _load_enrichment(r, target_date: date, watchlist: list[dict]) -> dict:
    """Load enrichment data from Redis to mimic strategy_runner._enrich_strategy5_params."""
    enrichment: dict[str, dict] = {}

    # Watchlist per-symbol data (screener score, gap data, bias)
    wl_by_sym = {w["symbol"]: w for w in watchlist}

    # Morning briefing
    briefing = {}
    br_raw = await r.get(f"strat5:morning_briefing:{target_date}")
    if br_raw:
        briefing = json.loads(br_raw)

    # India VIX
    india_vix = None
    vix_raw = await r.get("price:INDIA VIX")
    if vix_raw:
        try:
            vix_data = json.loads(vix_raw)
            india_vix = float(vix_data.get("ltp", 0) if isinstance(vix_data, dict) else vix_raw)
        except (json.JSONDecodeError, TypeError):
            try:
                india_vix = float(vix_raw)
            except (ValueError, TypeError):
                pass

    # RVOL baselines
    from app.indicators.rvol import deserialize_profile
    rvol_profiles: dict[str, dict] = {}
    for sym in wl_by_sym:
        rvol_raw = await r.get(f"strat5:rvol_baseline:{sym}")
        if rvol_raw:
            rvol_profiles[sym] = deserialize_profile(rvol_raw)

    for sym, wl_item in wl_by_sym.items():
        sym_params = {}
        sym_params["_screener_score"] = wl_item.get("composite_score", 0)
        sym_params["_stock_gap_pct"] = wl_item.get("gap_pct")
        sym_params["_relative_gap_pct"] = wl_item.get("relative_gap_pct")
        sym_params["_gap_direction"] = wl_item.get("gap_direction")
        sym_params["_stock_bias"] = wl_item.get("bias")
        sym_params["_stock_bias_source"] = wl_item.get("bias_source")
        sym_params["_briefing_approach"] = briefing.get("approach", "normal")
        sym_params["_briefing_max_lots"] = briefing.get("max_lots_recommendation", 2)
        sym_params["_briefing_sector_bias"] = briefing.get("sector_bias", "none")
        if india_vix:
            sym_params["_india_vix"] = india_vix
        if sym in rvol_profiles:
            sym_params["_rvol_profile"] = rvol_profiles[sym]
        enrichment[sym] = sym_params

    logger.info(f"Enrichment loaded: briefing={briefing.get('approach', '?')}, VIX={india_vix}, "
                f"RVOL profiles={len(rvol_profiles)}/{len(wl_by_sym)}")
    return enrichment


async def run_replay(target_date: date, symbol_filter: list[str] | None = None):
    r = get_redis()

    # Load watchlist from Redis
    raw = await r.get(f"strat5:watchlist:{target_date}")
    if not raw:
        logger.error(f"No watchlist found for {target_date} in Redis")
        return

    watchlist = json.loads(raw)
    symbols = [w["symbol"] for w in watchlist]
    if symbol_filter:
        symbols = [s for s in symbols if s in symbol_filter]

    logger.info(f"Replaying {len(symbols)} symbols for {target_date}: {symbols}")

    # Build base params
    params = dict(INTRADAY_FUTURES_DEFAULTS)
    params["_active_position_count"] = 0
    params["_daily_trade_count"] = 0

    # Load enrichment from Redis (screener scores, gap data, RVOL, briefing, VIX)
    enrichment = await _load_enrichment(r, target_date, watchlist)

    all_signals: list[dict] = []

    async with async_session_factory() as session:
        # Pre-fetch previous day levels and daily candles for all symbols
        prev_day_cache: dict[str, PreviousDayLevels | None] = {}
        daily_cache: dict[str, list[Candle]] = {}

        logger.info("Loading previous day levels and daily candles...")
        for sym in symbols:
            prev_day_cache[sym] = await get_previous_day_levels(session, sym, target_date)
            daily_cache[sym] = await fetch_daily_candles(session, sym, 30)
            status = "OK" if prev_day_cache[sym] else "MISSING"
            logger.info(f"  {sym}: prev_day={status}, daily_bars={len(daily_cache[sym])}")

        # Replay each symbol with enriched params
        for sym in symbols:
            logger.info(f"\n{'='*60}")
            logger.info(f"Replaying {sym}")
            logger.info(f"{'='*60}")

            sym_params = dict(params)
            sym_params.update(enrichment.get(sym, {}))

            # Log enrichment
            score = sym_params.get("_screener_score", 0)
            gap = sym_params.get("_stock_gap_pct")
            rvol = "yes" if "_rvol_profile" in sym_params else "no"
            logger.info(f"  Enriched: score={score}, gap={gap}, rvol={rvol}, "
                        f"bias={sym_params.get('_stock_bias')}, vix={sym_params.get('_india_vix')}")

            strategy = IntradayFuturesStrategy()
            sig = await replay_symbol(
                session, strategy, sym, target_date,
                prev_day_cache[sym], daily_cache[sym], sym_params,
            )
            all_signals.extend(sig)

    # Summary
    logger.info(f"\n{'='*60}")
    logger.info(f"REPLAY COMPLETE — {target_date}")
    logger.info(f"{'='*60}")
    logger.info(f"Symbols replayed: {len(symbols)}")
    logger.info(f"Total signals: {len(all_signals)}")

    if all_signals:
        logger.info(f"\nAll signals:")
        for s in all_signals:
            logger.info(
                f"  {s['time']} {s['symbol']:12s} {s['setup_type']:18s} "
                f"{s['signal_type']:8s} entry={s['entry_price']:>8.2f} "
                f"SL={s['stop_loss']:>8.2f} T={s['target_price']:>8.2f} "
                f"conf={s['confidence']:>5.1f} lots={s['lots']}"
            )

        # Group by setup type
        by_setup: dict[str, int] = {}
        for s in all_signals:
            by_setup[s["setup_type"]] = by_setup.get(s["setup_type"], 0) + 1
        logger.info(f"\nBy setup: {by_setup}")

    return all_signals


def main():
    parser = argparse.ArgumentParser(description="Replay Strategy 5 signal generation")
    parser.add_argument("--date", default=str(date.today()), help="Target date (YYYY-MM-DD)")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbol filter")
    args = parser.parse_args()

    target = date.fromisoformat(args.date)
    sym_filter = args.symbols.split(",") if args.symbols else None

    asyncio.run(run_replay(target, sym_filter))


if __name__ == "__main__":
    main()
