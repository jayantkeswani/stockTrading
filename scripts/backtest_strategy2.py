"""Backtest Strategy 2 (VWAP Pullback, index options): thesis-invalidation study.

Replays live-generated S2 signals (not re-generated) and simulates exits on the
OPTION PREMIUM, then compares baseline (SL / target / EOD) against an early
thesis-invalidation exit fired when the TRADED INDEX's intraday bias flips
against the option (STRONG-bearish kills a CE; STRONG-bullish kills a PE). This
is the options counterpart of `backtest_strategy5.py` and reuses its (futures-
volume-weighted) bias builder.

Premium valuation = delta approximation from the index move (fast mode):
    CE:  premium ≈ entry + δ·(index_now − index_entry)
    PE:  premium ≈ entry − δ·(index_now − index_entry)
This needs only INDEX candles, so it covers every signal regardless of whether
the (now-expired) option contract still has fetchable data. It ignores theta/vega
— a known fast-mode limitation; the baseline-vs-invalidation *comparison* is the
robust output, not the absolute premium. P&L is in PREMIUM POINTS per lot-unit
(size-independent; × option lot size for rupees).

Usage:
    cd backend && source .venv/bin/activate
    python scripts/backtest_strategy2.py --confidence 70 --start 2026-04-29 --end 2026-06-02 --invalidation --inval-persist 3
    python scripts/backtest_strategy2.py --inval-sweep --confidence 70 --start 2026-04-29 --end 2026-06-02
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.constants import IST, MARKET_CLOSE, MARKET_OPEN  # noqa: E402
from app.core.database import async_session_factory  # noqa: E402
from app.core.enums import DayBias  # noqa: E402
from app.indicators.vwap import calculate_vwap  # noqa: E402

# Shared replay helpers (futures-volume-weighted index bias) live in the S5 script.
from backtest_strategy5 import (  # noqa: E402
    _fetch_day_candles_ts,
    _fetch_fut_volume,
    _min_key,
    build_index_bias_series,
)

DEFAULT_DELTA = 0.50  # ATM option delta for the fast-mode premium approximation


@dataclass
class S2Trade:
    symbol: str          # index (NIFTY, BANKNIFTY, ...)
    signal_type: str     # BUY_CE / BUY_PE
    confidence: float
    index_entry: float
    prem_entry: float
    prem_sl: float
    prem_target: float | None
    entry_time: datetime
    exit_prem: float = 0.0
    exit_time: datetime | None = None
    exit_reason: str = ""
    pnl: float = 0.0           # premium points (exit_prem - prem_entry)
    inval_exit_prem: float = 0.0
    inval_exit_time: datetime | None = None
    inval_exit_reason: str = ""
    inval_pnl: float = 0.0
    inval_changed: bool = False
    inval_delta: float = 0.0

    @property
    def is_ce(self) -> bool:
        return self.signal_type in ("BUY_CE", "CE")


async def fetch_s2_signals(session, start_date, end_date, min_confidence):
    """Load vwap_pullback signals that carry an index entry price."""
    from sqlalchemy import text

    start_ts = datetime.combine(start_date, dt_time(0, 0), tzinfo=IST)
    end_ts = datetime.combine(end_date + timedelta(days=1), dt_time(0, 0), tzinfo=IST)
    result = await session.execute(
        text("""
            SELECT symbol, signal_type, entry_price, stop_loss, target_price,
                   confidence, generated_at, indicators
            FROM signals
            WHERE strategy_name = 'vwap_pullback'
              AND confidence >= :min_conf
              AND generated_at >= :start_ts AND generated_at < :end_ts
            ORDER BY generated_at
        """),
        {"min_conf": min_confidence, "start_ts": start_ts, "end_ts": end_ts},
    )
    sigs = []
    for r in result.all():
        ind = r.indicators or {}
        idx_entry = ind.get("index_entry_price")
        if idx_entry is None:
            continue
        sigs.append({
            "symbol": r.symbol,
            "signal_type": r.signal_type,
            "index_entry": float(idx_entry),
            "prem_entry": float(r.entry_price),
            "prem_sl": float(r.stop_loss),
            "prem_target": float(r.target_price) if r.target_price else None,
            "confidence": float(r.confidence),
            "generated_at": r.generated_at,
        })
    return sigs


async def build_index_vwap_series(session, index_symbol, d) -> dict[datetime, float]:
    """Index VWAP at every minute, weighted by near-month futures volume (for the quorum)."""
    day_start = datetime.combine(d, MARKET_OPEN, tzinfo=IST)
    day_end = datetime.combine(d, MARKET_CLOSE, tzinfo=IST)
    candles_ts = await _fetch_day_candles_ts(session, index_symbol, day_start, day_end)
    fut_vol = await _fetch_fut_volume(session, f"{index_symbol}_FUT", day_start, day_end)
    series: dict[datetime, float] = {}
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    vols: list[int] = []
    for ts_i, c in candles_ts:
        highs.append(c.high)
        lows.append(c.low)
        closes.append(c.close)
        vols.append(fut_vol.get(_min_key(ts_i), 0))
        vw = calculate_vwap(highs, lows, closes, vols)
        series[_min_key(ts_i)] = vw.vwap if vw else c.close
    return series


def simulate_exit_s2(
    prem_entry, prem_sl, prem_target, is_ce, index_entry, index_candles, close_deadline,
    delta=DEFAULT_DELTA, bias_by_min=None, vwap_by_min=None,
    inval_persist=0, inval_quorum=False, inval_strong_only=True,
):
    """Walk index candles, delta-approximate the premium, return (exit_prem, ts, reason).

    Exit priority within a candle: premium SL → target → invalidation (close) → time.
    Invalidation only ever exits earlier than the baseline.
    """
    inval_count = 0
    last_prem, last_ts = prem_entry, close_deadline
    for ts, c in index_candles:
        move = c.close - index_entry
        prem = prem_entry + delta * move if is_ce else prem_entry - delta * move
        prem = max(prem, 0.05)
        last_prem, last_ts = prem, ts

        # 1. Premium SL (a BUY option's SL is below entry)
        if prem <= prem_sl:
            return prem_sl, ts, "SL"
        # 2. Premium target
        if prem_target is not None and prem >= prem_target:
            return prem_target, ts, "TARGET"
        # 2.5 Thesis-invalidation: index bias opposes the option direction
        if inval_persist > 0 and bias_by_min is not None:
            nb = bias_by_min.get(_min_key(ts))
            if nb is not None:
                opposing = (
                    (is_ce and nb.bias == DayBias.BEARISH)
                    or (not is_ce and nb.bias == DayBias.BULLISH)
                )
                if opposing and (not inval_strong_only or nb.strength == "STRONG"):
                    inval_count += 1
                else:
                    inval_count = 0
                if inval_count >= inval_persist:
                    ok = True
                    if inval_quorum:
                        vw = vwap_by_min.get(_min_key(ts)) if vwap_by_min else None
                        if vw is None:
                            ok = False
                        elif is_ce:
                            ok = c.close < vw   # CE thesis broken: index below VWAP
                        else:
                            ok = c.close > vw   # PE thesis broken: index above VWAP
                    if ok:
                        return prem, ts, "INVALIDATION"
        # 3. EOD time exit
        if ts >= close_deadline:
            return prem, ts, "TIME_EXIT"

    return last_prem, last_ts, "DATA_END"


async def run_backtest_s2(
    start_date, end_date, min_confidence, *, delta=DEFAULT_DELTA, quiet=False,
    invalidation=False, inval_persist=3, inval_quorum=False, inval_strong_only=True,
    _caches=None,
):
    caches = _caches if _caches is not None else {"bias": {}, "gms": {}, "vwap": {}}
    trades: list[S2Trade] = []

    async with async_session_factory() as session:
        sigs = await fetch_s2_signals(session, start_date, end_date, min_confidence)
        for sig in sigs:
            gen_at = sig["generated_at"]
            d = gen_at.date()
            close_deadline = datetime.combine(d, dt_time(15, 15), tzinfo=IST)
            day_end = datetime.combine(d, MARKET_CLOSE, tzinfo=IST)
            index_candles = await _fetch_day_candles_ts(session, sig["symbol"], gen_at, day_end)
            if not index_candles:
                continue

            t = S2Trade(
                symbol=sig["symbol"], signal_type=sig["signal_type"],
                confidence=sig["confidence"], index_entry=sig["index_entry"],
                prem_entry=sig["prem_entry"], prem_sl=sig["prem_sl"],
                prem_target=sig["prem_target"], entry_time=gen_at,
            )
            ex_p, ex_t, ex_r = simulate_exit_s2(
                t.prem_entry, t.prem_sl, t.prem_target, t.is_ce, t.index_entry,
                index_candles, close_deadline, delta=delta,
            )
            t.exit_prem, t.exit_time, t.exit_reason = round(ex_p, 2), ex_t, ex_r
            t.pnl = round(ex_p - t.prem_entry, 2)

            if invalidation:
                bkey = (sig["symbol"], d)
                nbs = caches["bias"].get(bkey)
                if nbs is None:
                    nbs = await build_index_bias_series(session, sig["symbol"], d, caches["gms"])
                    caches["bias"][bkey] = nbs
                vwap_s = None
                if inval_quorum:
                    vwap_s = caches["vwap"].get(bkey)
                    if vwap_s is None:
                        vwap_s = await build_index_vwap_series(session, sig["symbol"], d)
                        caches["vwap"][bkey] = vwap_s
                iv_p, iv_t, iv_r = simulate_exit_s2(
                    t.prem_entry, t.prem_sl, t.prem_target, t.is_ce, t.index_entry,
                    index_candles, close_deadline, delta=delta,
                    bias_by_min=nbs, vwap_by_min=vwap_s,
                    inval_persist=inval_persist, inval_quorum=inval_quorum,
                    inval_strong_only=inval_strong_only,
                )
                t.inval_exit_prem, t.inval_exit_time, t.inval_exit_reason = round(iv_p, 2), iv_t, iv_r
                t.inval_pnl = round(iv_p - t.prem_entry, 2)
                t.inval_changed = iv_r == "INVALIDATION"
                t.inval_delta = round(t.inval_pnl - t.pnl, 2)
            trades.append(t)

    if not quiet:
        print(f"\nLoaded {len(trades)} S2 trades (confidence >= {min_confidence}, delta={delta})")
    return trades


def _net(trades, inval=False):
    return sum((t.inval_pnl if inval else t.pnl) for t in trades)


def print_s2_report(trades, persist, quorum, strong_only):
    if not trades:
        print("  No trades.")
        return
    n = len(trades)
    base_net = _net(trades)
    inval_net = _net(trades, inval=True)
    changed = [t for t in trades if t.inval_changed]
    helped = [t for t in changed if t.inval_delta > 0]
    hurt = [t for t in changed if t.inval_delta < 0]

    print(f"\n{'='*84}")
    print(f"  STRATEGY 2 — THESIS-INVALIDATION EXIT  vs  BASELINE   (premium points/lot-unit)")
    print(f"  trigger: index bias opposes option for {persist} candle(s) "
          f"[{'STRONG-only' if strong_only else 'MODERATE+'}, quorum={'ON' if quorum else 'off'}]")
    print(f"{'='*84}\n")
    print(f"  Baseline net (SL/target/EOD):  {base_net:>+12,.1f}")
    print(f"  With invalidation exit:        {inval_net:>+12,.1f}")
    print(f"  Net impact:                    {inval_net - base_net:>+12,.1f}")
    print(f"\n  Trades changed: {len(changed)} / {n}   "
          f"helped {len(helped)} (+{sum(t.inval_delta for t in helped):,.1f}), "
          f"hurt {len(hurt)} ({sum(t.inval_delta for t in hurt):,.1f})")

    print(f"\n  By option type:")
    for label, is_ce in (("CE (bullish)", True), ("PE (bearish)", False)):
        sub = [t for t in trades if t.is_ce == is_ce]
        if not sub:
            continue
        b, iv = _net(sub), _net(sub, inval=True)
        ch = sum(1 for t in sub if t.inval_changed)
        print(f"    {label:<13} base {b:>+11,.1f}  ->  inval {iv:>+11,.1f}   (delta {iv - b:>+10,.1f}, {ch} chg)")

    print(f"\n  Daily baseline -> invalidation:")
    days: dict[date, list[S2Trade]] = {}
    for t in trades:
        days.setdefault(t.entry_time.date(), []).append(t)
    for d in sorted(days):
        dt = days[d]
        b, iv = _net(dt), _net(dt, inval=True)
        ch = sum(1 for t in dt if t.inval_changed)
        mark = f"   <-- {ch} chg" if ch else ""
        print(f"    {d}   base {b:>+11,.1f}  ->  inval {iv:>+11,.1f}   (delta {iv - b:>+10,.1f}){mark}")
    print()


async def run_inval_sweep_s2(start_date, end_date, min_confidence, delta=DEFAULT_DELTA, strong_only=True):
    caches: dict = {"bias": {}, "gms": {}, "vwap": {}}
    base = await run_backtest_s2(start_date, end_date, min_confidence, delta=delta, quiet=True, _caches=caches)
    base_net = _net(base)
    n = len(base)
    print(f"\n{'='*88}")
    print(f"  S2 INVALIDATION SWEEP — Confidence >= {min_confidence:g} [bias "
          f"{'STRONG-only' if strong_only else 'MODERATE+'}, delta={delta}]   {start_date} to {end_date}")
    print(f"{'='*88}\n")
    if n == 0:
        print("  No trades.\n")
        return
    print(f"  Baseline (no invalidation): {base_net:>+11,.1f} premium pts   {n} trades\n")
    print(f"  {'persist':>7} {'quorum':>6} {'changed':>7} {'savings':>11} {'cost':>11} "
          f"{'net delta':>11} {'inval net':>11}")
    print(f"  {'-'*7} {'-'*6} {'-'*7} {'-'*11} {'-'*11} {'-'*11} {'-'*11}")
    for quorum in (False, True):
        for persist in (1, 2, 3, 5):
            tr = await run_backtest_s2(
                start_date, end_date, min_confidence, delta=delta, quiet=True,
                invalidation=True, inval_persist=persist, inval_quorum=quorum,
                inval_strong_only=strong_only, _caches=caches,
            )
            changed = [t for t in tr if t.inval_changed]
            savings = sum(t.inval_delta for t in changed if t.inval_delta > 0)
            cost = sum(t.inval_delta for t in changed if t.inval_delta < 0)
            inval_net = _net(tr, inval=True)
            print(f"  {persist:>7} {('ON' if quorum else 'off'):>6} {len(changed):>7} "
                  f"{savings:>+11,.1f} {cost:>+11,.1f} {inval_net - base_net:>+11,.1f} {inval_net:>+11,.1f}")
    print()


def _parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def main():
    p = argparse.ArgumentParser(description="Backtest Strategy 2 thesis-invalidation exit")
    p.add_argument("--confidence", type=float, default=70)
    p.add_argument("--start", type=_parse_date, required=True)
    p.add_argument("--end", type=_parse_date, default=None)
    p.add_argument("--delta", type=float, default=DEFAULT_DELTA, help="Option delta for premium approximation")
    p.add_argument("--invalidation", action="store_true")
    p.add_argument("--inval-persist", type=int, default=3)
    p.add_argument("--inval-quorum", action="store_true")
    p.add_argument("--inval-moderate", action="store_true")
    p.add_argument("--inval-sweep", action="store_true")
    args = p.parse_args()
    end_date = args.end or args.start

    async def _run():
        if args.inval_sweep:
            await run_inval_sweep_s2(args.start, end_date, args.confidence,
                                     delta=args.delta, strong_only=not args.inval_moderate)
        else:
            trades = await run_backtest_s2(
                args.start, end_date, args.confidence, delta=args.delta,
                invalidation=args.invalidation, inval_persist=args.inval_persist,
                inval_quorum=args.inval_quorum, inval_strong_only=not args.inval_moderate,
            )
            if args.invalidation:
                print_s2_report(trades, args.inval_persist, args.inval_quorum, not args.inval_moderate)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
