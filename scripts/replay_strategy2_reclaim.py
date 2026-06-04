"""Replay the S2 *reclaim-entry* redesign (strategy_name='vwap_reclaim') over a window.

OFFLINE prototype + head-to-head validation for the entry redesign motivated by
`docs/backtest/s2-signal-accuracy-study.md` (root cause: S2 fires on the reversal *candle*
at VWAP, which is exhaustion — 55% of losing entries never go green). The index-options
analogue of the S5→S6 move. Mirrors `replay_strategy6.py`: it drives the **live**
`VWAPReclaimStrategy` (Strategy 7) minute-by-minute over the INDEX spot candles already
staged in `stocktrading_bt`, so the offline validation runs the exact live entry logic —
arm on S2's VWAP-reversal trigger, fire only on a 1m reclaim of the reversal extreme, tight
swing stop, R:R target.

The index VWAP is weighted by the near-month `{index}_FUT` volume (index spot volume is
~zero), matching live + `build_index_bias_series`. Signals are stamped with the real
reclaim-candle timestamp (basis 0; index spot IS the underlying) and persisted as
strategy_name='vwap_reclaim' so the analyzer scores first-touch:

    DATABASE_URL=...stocktrading_bt python scripts/replay_strategy2_reclaim.py \
        --start 2026-04-29 --end 2026-06-04
    DATABASE_URL=...stocktrading_bt python scripts/analyze_strategy2_signal_accuracy.py \
        --strategy vwap_reclaim --start 2026-04-29 --end 2026-06-04

Universe: the days/indices the live `vwap_pullback` strategy fired on (a clean head-to-head
vs S2 on the same index-days). `--no-persist` to dry-run; `--target-mode` {rr,structure},
`--reclaim-ref` {reversal_extreme,vwap}, `--reclaim-timeout`, `--rr` sweep the spec's open
design questions. Confidence is UNGATED here (the S2 composite is inverted — study §3); the
replay persists every reclaim and a lean structural confidence is recorded for calibration.

Fidelity caveats (offline approximations, as in the S6 replay): intraday_bias uses the
cached global cues but no live nifty_bias injection; OI/VIX are omitted (used only by the
soft confidence/structural target, not the gate). The volume-spike arm filter reads the
merged FUT 5m volume from candles_1m (candles_5m_futures_volume left None) — equivalent to
live's separate FUT 5m series. Trust the DIRECTION of the head-to-head, then confirm the
magnitude on the live shadow real-P&L A/B.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.core.constants import IST, MARKET_CLOSE, MARKET_OPEN
from app.core.database import async_session_factory
from app.indicators.candle_patterns import Candle
from app.indicators.cpr import calculate_cpr
from app.indicators.intraday_bias import compute_intraday_bias
from app.indicators.vwap import calculate_vwap
from app.services.strategy_params import VWAP_RECLAIM_DEFAULTS
from app.strategies.base import MarketContext
from app.strategies.strategy_7_vwap_reclaim import VWAPReclaimStrategy

# Reuse the exact staged-data helpers the other backtest scripts use.
from backtest_strategy5 import (
    _fetch_day_candles_ts,
    _fetch_fut_volume,
    _get_previous_day_levels,
    _global_cues_cached,
    _min_key,
    parse_date,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

INDICES = ("NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "MIDCPNIFTY")


# ── DB helpers ────────────────────────────────────────────────────────────────

async def fetch_signal_universe(session, start: date, end: date) -> dict[date, list[str]]:
    """Map each trading day → the INDICES the live vwap_pullback strategy fired on."""
    from sqlalchemy import text
    result = await session.execute(
        text("""
            SELECT DISTINCT (generated_at AT TIME ZONE 'Asia/Kolkata')::date AS d, symbol
            FROM signals
            WHERE strategy_name = 'vwap_pullback'
              AND generated_at >= :start AND generated_at < :end
            ORDER BY d, symbol
        """),
        {"start": datetime.combine(start, dt_time(0, 0), tzinfo=IST),
         "end": datetime.combine(end + timedelta(days=1), dt_time(0, 0), tzinfo=IST)},
    )
    universe: dict[date, list[str]] = {}
    for r in result.all():
        if r.symbol in INDICES:
            universe.setdefault(r.d, []).append(r.symbol)
    return universe


async def _merged_index_candles(session, index: str, day: date) -> list[tuple[datetime, Candle]]:
    """Index spot 1m OHLC with the near-month FUT volume merged in (for VWAP + the
    volume filter). Index spot volume is ~zero, so the FUT volume is what counts."""
    day_start = datetime.combine(day, MARKET_OPEN, tzinfo=IST)
    day_end = datetime.combine(day, MARKET_CLOSE, tzinfo=IST)
    candles_ts = await _fetch_day_candles_ts(session, index, day_start, day_end)
    fut_vol = await _fetch_fut_volume(session, f"{index}_FUT", day_start, day_end)
    return [
        (ts, Candle(open=c.open, high=c.high, low=c.low, close=c.close,
                    volume=fut_vol.get(_min_key(ts), 0)))
        for ts, c in candles_ts
    ]


async def _nifty_change_by_min(session, day: date) -> dict[datetime, float]:
    """Per-minute NIFTY % change vs the day's open (with-trend context for analysis)."""
    day_start = datetime.combine(day, MARKET_OPEN, tzinfo=IST)
    day_end = datetime.combine(day, MARKET_CLOSE, tzinfo=IST)
    candles_ts = await _fetch_day_candles_ts(session, "NIFTY", day_start, day_end)
    if not candles_ts:
        return {}
    n_open = candles_ts[0][1].open
    if not n_open:
        return {}
    return {_min_key(ts): round((c.close - n_open) / n_open * 100, 3) for ts, c in candles_ts}


# ── Replay driver (drives the live VWAPReclaimStrategy) ─────────────────────────

async def replay_index_day(session, index: str, day: date, params: dict, gms_cache: dict) -> list[dict]:
    """Drive the live VWAPReclaimStrategy minute-by-minute over one index-day."""
    candles_ts = await _merged_index_candles(session, index, day)
    if len(candles_ts) < 10:
        return []
    prev_day = await _get_previous_day_levels(session, index, day)
    cpr = calculate_cpr(prev_day.pdh, prev_day.pdl, prev_day.pdc) if prev_day else None
    ndc_by_min = await _nifty_change_by_min(session, day)

    strategy = VWAPReclaimStrategy()
    out: list[dict] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    vols: list[int] = []
    running: list[Candle] = []

    for ts, c in candles_ts:
        running.append(c)
        highs.append(c.high)
        lows.append(c.low)
        closes.append(c.close)
        vols.append(c.volume)
        vwap = calculate_vwap(highs, lows, closes, vols)
        gcues = await _global_cues_cached(session, ts, gms_cache)
        bias = compute_intraday_bias(
            prev_day=prev_day, candles_1m=running, vwap=vwap, current_price=c.close,
            global_cues=gcues, as_of=ts,
        ) if prev_day else None

        ctx = MarketContext(
            symbol=index, current_price=c.close, candles_5m=[], vwap=vwap,
            previous_day=prev_day, cpr=cpr, oi_analysis=None, india_vix=None,
            current_time_ist=ts.isoformat(), global_cues=gcues, intraday_bias=bias,
            strategy_params=params, candles_1m=running, candles_5m_futures_volume=None,
        )
        sig = strategy.evaluate(ctx)
        strategy.drain_pending_logs()
        if sig is not None:
            ndc = ndc_by_min.get(_min_key(ts))
            indicators = dict(sig.indicators)
            if ndc is not None:
                indicators["nifty_day_change_pct"] = ndc
            out.append({
                "symbol": index,
                "signal_type": sig.signal_type.value,
                # Offline: no option resolution — persist the INDEX-level geometry. The
                # analyzer reads index_sl/index_target/index_entry_price from indicators.
                "entry_price": round(sig.entry_price, 2),
                "stop_loss": round(sig.index_sl, 2) if sig.index_sl is not None else 0.0,
                "target_price": round(sig.index_target, 2) if sig.index_target is not None else None,
                "confidence": sig.confidence,
                "reason": sig.reason,
                "generated_at": ts,
                "indicators": indicators,
            })
    return out


async def persist_signals(session, signals: list[dict], start: date, end: date) -> None:
    """Replace vwap_reclaim signals in the window with the freshly replayed set."""
    from sqlalchemy import text
    await session.execute(
        text("""DELETE FROM signals WHERE strategy_name='vwap_reclaim'
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
                    gen_random_uuid(), 'vwap_reclaim', :symbol, :signal_type, 0,
                    :expiry_date, :entry_price, :stop_loss, :target_price, :confidence,
                    'PENDING', :reason, (:indicators)::jsonb, :generated_at, true, 'OPTION', false,
                    now(), now()
                )
            """),
            {**{k: s[k] for k in ("symbol", "signal_type", "entry_price", "stop_loss",
                                  "target_price", "confidence", "reason", "generated_at")},
             "expiry_date": s["generated_at"].date(),
             "indicators": json.dumps(s["indicators"], default=str)},
        )
    await session.commit()


async def run(start: date, end: date, symbol_filter, persist: bool, params: dict) -> None:
    async with async_session_factory() as session:
        universe = await fetch_signal_universe(session, start, end)
        if not universe:
            logger.error("No vwap_pullback signals in window — nothing defines the replay universe.")
            return
        gms_cache: dict = {}
        all_signals: list[dict] = []

        for day in sorted(universe):
            idxs = universe[day]
            if symbol_filter:
                idxs = [s for s in idxs if s in symbol_filter]
            if not idxs:
                continue
            day_sigs: list[dict] = []
            for idx in idxs:
                day_sigs += await replay_index_day(session, idx, day, params, gms_cache)
            ce = sum(1 for s in day_sigs if s["signal_type"] == "BUY_CE")
            logger.info("%s: %d indices → %d signals (%d CE / %d PE)",
                        day, len(idxs), len(day_sigs), ce, len(day_sigs) - ce)
            all_signals += day_sigs

        ce = sum(1 for s in all_signals if s["signal_type"] == "BUY_CE")
        logger.info("TOTAL: %d vwap_reclaim signals (%d CE / %d PE)  [target=%s reclaim_ref=%s]",
                    len(all_signals), ce, len(all_signals) - ce,
                    params["target_mode"], params["reclaim_ref"])

        if persist:
            await persist_signals(session, all_signals, start, end)
            logger.info("Persisted %d signals (strategy_name=vwap_reclaim).", len(all_signals))


def main() -> None:
    p = argparse.ArgumentParser(description="Replay the S2 reclaim-entry redesign (vwap_reclaim)")
    p.add_argument("--start", type=parse_date, required=True)
    p.add_argument("--end", type=parse_date, default=None)
    p.add_argument("--symbols", default=None, help="comma-separated index filter")
    p.add_argument("--no-persist", action="store_true", help="don't write signals to the DB")
    p.add_argument("--target-mode", choices=["rr", "structure"], default=None)
    p.add_argument("--reclaim-ref", choices=["reversal_extreme", "vwap"], default=None)
    p.add_argument("--reclaim-timeout", type=int, default=None)
    p.add_argument("--rr", type=float, default=None, help="R:R multiplier for the target")
    args = p.parse_args()

    params = dict(VWAP_RECLAIM_DEFAULTS)
    if args.target_mode:
        params["target_mode"] = args.target_mode
    if args.reclaim_ref:
        params["reclaim_ref"] = args.reclaim_ref
    if args.reclaim_timeout is not None:
        params["reclaim_timeout"] = args.reclaim_timeout
    if args.rr is not None:
        params["rr_multiplier"] = args.rr

    asyncio.run(run(
        args.start, args.end or args.start,
        args.symbols.split(",") if args.symbols else None,
        not args.no_persist, params,
    ))


if __name__ == "__main__":
    main()
