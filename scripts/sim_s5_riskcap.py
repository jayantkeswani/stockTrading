"""Simulate the filtered + risk-capped S5 shadow book (request-specific overlay study).

Replays the REAL production SHADOW ``intraday_futures`` trades (staged into bt table
``s5_shadow`` from prod ``trades`` JOIN signal_snapshot) through three NESTED policies,
on the same 1m-candle exit engine as ``backtest_strategy5.py --source shadow``:

  A. BASELINE        — filtered trades, structural SL / target / trailing / time exits
  B. +PER-TRADE CAP  — additionally cap each trade's loss at ``--per-trade-cap`` rupees
                       (an MTM stop, ₹/lot; shadow is always 1 lot). Implemented by
                       tightening the hard stop to the CLOSER of (structural SL, cap
                       level); an exit that fires there is labeled ``MTM_CAP``.
  C. +DAILY STOP     — additionally halt the day once booked (realized) P&L falls to
                       ``-daily-stop``; trades ENTERED at/after the halt timestamp are
                       dropped. Open positions run to their natural exit (``halt`` mode),
                       so a day's loss can exceed the cap via in-flight positions. The
                       stricter ``flatten`` mode also force-closes open positions at the
                       breach minute.

Filters (defaults match the request): setup in {ORB, PDH_PDL}, adr_pct >= 2.8,
confidence >= 40. The ADR + setup are read from the trade's signal_snapshot indicators.
Trades whose snapshot geometry is degenerate (target/stop not strictly beyond the FILL
price in the trade direction — entry-fill vs signal-level drift) or that have no candles
in the window are dropped and counted (they cannot be first-touch simulated).

ENGINE CAVEAT (inherited from backtest_strategy5): the 1m first-touch engine runs ~3.6x
optimistic vs live tick exits. Trust the *relative* A->B->C deltas and direction, NOT the
absolute magnitude. The real prod realized P&L of the same filtered trades (no overlays)
is printed as a calibration anchor.

    DATABASE_URL=postgresql+asyncpg://trader:trader_dev_123@localhost:5433/stocktrading_bt \
        python scripts/sim_s5_riskcap.py
    # parameter sweep example
    DATABASE_URL=...bt python scripts/sim_s5_riskcap.py --per-trade-cap 12000 --daily-stop 25000
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent))                       # scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))    # backend/

from app.core.constants import IST, MARKET_CLOSE                               # noqa: E402
from app.services.strategy_params import get_strategy_params                   # noqa: E402
from backtest_strategy5 import fetch_candles_after, simulate_exit              # noqa: E402


# ── Data structures ─────────────────────────────────────────────────────

@dataclass
class Trade:
    symbol: str
    is_long: bool
    setup: str
    confidence: float
    adr: float
    entry_price: float
    structural_sl: float
    target: float | None
    quantity: int            # shares in 1 lot (shadow == 1 lot)
    entry_time: datetime
    real_pnl: float | None   # actual prod realized P&L (no overlays) — calibration anchor
    real_exit_time: datetime | None  # actual prod exit timestamp (faithful overlay window)
    # Filled by the re-sim engine (policies A/B/C):
    exit_time: datetime | None = None
    base_pnl: float = 0.0    # A: structural exits
    base_reason: str = ""
    cap_pnl: float = 0.0     # B: with per-trade MTM cap
    cap_reason: str = ""
    cap_bound: bool = False  # did the cap fire (vs structural/target/trail/time)?
    # Filled by the faithful overlay (policies D/E/F — applied to REAL outcomes):
    faithful_pnl: float = 0.0
    faithful_cap_bound: bool = False
    faithful_exit_time: datetime | None = None
    mae_rupees: float = 0.0        # worst intra-life adverse MTM (<=0), 1m WICK basis (touch)
    mae_close_rupees: float = 0.0  # worst adverse MTM on 1m CLOSE (a ~2s LTP poll misses wicks)


# ── DB fetch ────────────────────────────────────────────────────────────

async def fetch_filtered(session, setups, min_adr, min_conf, start, end):
    """Load the filtered, orientation-valid shadow S5 trades from the staging table."""
    from sqlalchemy import text

    start_ts = datetime.combine(start, dt_time(0, 0), tzinfo=IST)
    end_ts = datetime.combine(end + timedelta(days=1), dt_time(0, 0), tzinfo=IST)
    rows = (await session.execute(
        text("""
            SELECT symbol, side, entry_price, entry_time, quantity, signal_confidence,
                   net_pnl, exit_time, snap_sl, snap_tgt, setup, adr_pct::float AS adr
            FROM s5_shadow
            WHERE setup = ANY(:setups)
              AND adr_pct ~ '^[0-9]+\\.?[0-9]*$' AND adr_pct::float >= :min_adr
              AND signal_confidence >= :min_conf
              AND entry_time >= :start_ts AND entry_time < :end_ts
              AND snap_sl IS NOT NULL
            ORDER BY entry_time
        """),
        {"setups": list(setups), "min_adr": min_adr, "min_conf": min_conf,
         "start_ts": start_ts, "end_ts": end_ts},
    )).all()

    trades, degenerate = [], 0
    for r in rows:
        is_long = r.side == "BUY"
        e, sl, tgt = float(r.entry_price), float(r.snap_sl), (float(r.snap_tgt) if r.snap_tgt is not None else None)
        # Orientation: SL strictly adverse AND (no target OR target strictly favorable).
        sl_ok = (sl < e) if is_long else (sl > e)
        tgt_ok = tgt is None or ((tgt > e) if is_long else (tgt < e))
        if not (sl_ok and tgt_ok):
            degenerate += 1
            continue
        trades.append(Trade(
            symbol=r.symbol, is_long=is_long, setup=r.setup,
            confidence=float(r.signal_confidence or 0.0), adr=float(r.adr),
            entry_price=e, structural_sl=sl, target=tgt,
            quantity=int(r.quantity or 1), entry_time=r.entry_time,
            real_pnl=float(r.net_pnl) if r.net_pnl is not None else None,
            real_exit_time=r.exit_time,
        ))
    return trades, degenerate


# ── Simulation ──────────────────────────────────────────────────────────

def _pnl(entry, exit_px, is_long, qty):
    return (exit_px - entry) * (1 if is_long else -1) * qty


async def simulate(session, trades, per_trade_cap, breakeven_pct, trail_pct):
    """Populate each trade's baseline (A) and per-trade-cap (B) exits. Returns the
    subset that had candles (others are dropped + counted by the caller)."""
    simulated, no_candle, bad_candle = [], 0, 0
    for t in trades:
        sig_date = t.entry_time.astimezone(IST).date()
        close_deadline = datetime.combine(sig_date, dt_time(15, 15), tzinfo=IST)
        day_end = datetime.combine(sig_date, MARKET_CLOSE, tzinfo=IST)
        candles = await fetch_candles_after(session, t.symbol, t.entry_time, day_end)
        if not candles:
            no_candle += 1
            continue
        # Wrong-instrument guard: the fill price must lie within the symbol's own candle
        # range for the day. A large gap means the trade was resolved to a DIFFERENT
        # instrument than the candles (the known 'BSE'->BANKEX mis-resolution: index-scale
        # entry ~60,000 against stock candles ~4,000). Such a trade's SL/target are ALSO at
        # the wrong scale, so it cannot be price-corrected — drop it from all policies.
        lo = min(l for _, o, h, l, c in candles)
        hi = max(h for _, o, h, l, c in candles)
        if t.entry_price < lo * 0.85 or t.entry_price > hi * 1.15:
            bad_candle += 1
            continue

        # A. Baseline (structural stop)
        bx, btime, breason, _, _ = simulate_exit(
            entry_price=t.entry_price, stop_loss=t.structural_sl, target_price=t.target,
            is_long=t.is_long, candles=candles, close_deadline=close_deadline,
            breakeven_pct=breakeven_pct, trail_pct=trail_pct, sl_mode="close",
        )
        t.exit_time = btime
        t.base_pnl = round(_pnl(t.entry_price, bx, t.is_long, t.quantity), 2)
        t.base_reason = breason

        # B. Per-trade MTM cap: tighten the hard stop to the CLOSER of structural / cap.
        cap_dist = per_trade_cap / t.quantity
        cap_level = t.entry_price - cap_dist if t.is_long else t.entry_price + cap_dist
        if t.is_long:
            eff_sl = max(t.structural_sl, cap_level)
        else:
            eff_sl = min(t.structural_sl, cap_level)
        cap_tighter = eff_sl != t.structural_sl

        cx, ctime, creason, _, _ = simulate_exit(
            entry_price=t.entry_price, stop_loss=eff_sl, target_price=t.target,
            is_long=t.is_long, candles=candles, close_deadline=close_deadline,
            breakeven_pct=breakeven_pct, trail_pct=trail_pct, sl_mode="close",
        )
        t.cap_pnl = round(_pnl(t.entry_price, cx, t.is_long, t.quantity), 2)
        # The cap "bound" only if the tightened hard stop is what fired (reason SL).
        t.cap_bound = cap_tighter and creason == "SL"
        t.cap_reason = "MTM_CAP" if t.cap_bound else creason
        # Keep the per-trade-cap exit time for the daily-stop event ordering.
        t.exit_time = ctime
        simulated.append(t)
    return simulated, no_candle, bad_candle


def apply_daily_stop(trades, daily_stop, pnl_attr="cap_pnl", exit_attr="exit_time"):
    """Realized-halt daily breaker.

    Within each IST day, accumulate realized (closed) P&L in trade-exit order; once it
    falls to <= -daily_stop the day is halted at that exit timestamp and every trade
    ENTERED at/after the halt is dropped. Already-open positions run to their natural
    exit (so a day's loss can still exceed the cap via in-flight positions).

    ``pnl_attr``/``exit_attr`` select which P&L + exit-time fields to use, so the same
    breaker drives both the re-sim book (cap_pnl/exit_time) and the faithful book
    (faithful_pnl/faithful_exit_time).

    Returns (taken, dropped, halt_times{day: ts}).
    """
    by_day = defaultdict(list)
    for t in trades:
        by_day[t.entry_time.astimezone(IST).date()].append(t)

    taken, dropped, halt_times = [], [], {}
    for d, day_trades in by_day.items():
        day_trades.sort(key=lambda t: t.entry_time)
        realized = 0.0
        open_q: list[tuple[datetime, float]] = []   # (exit_time, pnl) of taken trades
        halt_at: datetime | None = None

        def settle(up_to: datetime):
            nonlocal realized, halt_at
            open_q.sort(key=lambda x: x[0])
            while open_q and open_q[0][0] <= up_to:
                _, pnl = open_q.pop(0)
                realized += pnl
                if halt_at is None and realized <= -daily_stop:
                    halt_at = _

        for t in day_trades:
            settle(t.entry_time)
            if halt_at is not None and t.entry_time >= halt_at:
                dropped.append(t)
                continue
            taken.append(t)
            open_q.append((getattr(t, exit_attr), getattr(t, pnl_attr)))
        # drain remaining open trades (no more entries to block, but record the halt)
        for et, pnl in sorted(open_q, key=lambda x: x[0]):
            realized += pnl
            if halt_at is None and realized <= -daily_stop:
                halt_at = et
        if halt_at is not None:
            halt_times[d] = halt_at
    return taken, dropped, halt_times


async def faithful_overlay(session, trades, per_trade_cap):
    """Apply the per-trade cap to each trade's REAL realized outcome.

    Keeps the actual prod ``net_pnl`` and overrides it to ``-per_trade_cap`` ONLY when the
    trade's intra-life adverse excursion (MAE, from 1m candle wicks over [entry, real
    exit]) reached the floor. The 1m engine is used only to DETECT the floor breach, not
    to re-run the exit — so real outcomes are preserved for every non-breaching trade and
    the trailing-engine's directional distortion never enters. Only trades with both a
    real P&L and a real exit time are eligible.

    Returns the eligible subset (with faithful_* fields populated).
    """
    out = []
    for t in trades:
        if t.real_pnl is None or t.real_exit_time is None:
            continue
        candles = await fetch_candles_after(session, t.symbol, t.entry_time, t.real_exit_time)
        bound, cap_time, mae, mae_close = False, t.real_exit_time, 0.0, 0.0
        for ts, o, h, l, c in candles:
            # Ignore a stray bad tick (>25% from the fill is non-physical intraday) so one
            # corrupt 1m print can't poison the MAE-based cap.
            adverse_px = l if t.is_long else h
            if abs(adverse_px - t.entry_price) / t.entry_price > 0.25:
                continue
            adverse = ((l - t.entry_price) if t.is_long else (t.entry_price - h)) * t.quantity
            adverse_c = ((c - t.entry_price) if t.is_long else (t.entry_price - c)) * t.quantity
            mae = min(mae, adverse)
            mae_close = min(mae_close, adverse_c)
            if not bound and adverse <= -per_trade_cap:
                bound, cap_time = True, ts
        t.mae_rupees = round(mae, 2)
        t.mae_close_rupees = round(mae_close, 2)
        t.faithful_cap_bound = bound
        t.faithful_pnl = round(-per_trade_cap if bound else t.real_pnl, 2)
        t.faithful_exit_time = cap_time
        out.append(t)
    return out


# ── Reporting ───────────────────────────────────────────────────────────

def _stats(pnls: list[float]) -> dict:
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_w = sum(wins)
    gross_l = abs(sum(losses))
    return {
        "n": n, "net": sum(pnls),
        "wins": len(wins), "losses": len(losses),
        "hit": (len(wins) / n * 100) if n else 0.0,
        "avg_w": (gross_w / len(wins)) if wins else 0.0,
        "avg_l": (-gross_l / len(losses)) if losses else 0.0,
        "pf": (gross_w / gross_l) if gross_l > 0 else float("inf"),
        "exp": (sum(pnls) / n) if n else 0.0,
    }


def _daily_pnl(trades, attr) -> dict[date, float]:
    by_day: dict[date, float] = defaultdict(float)
    for t in trades:
        by_day[t.entry_time.astimezone(IST).date()] += getattr(t, attr)
    return by_day


def _curve_stats(daily: dict[date, float]):
    """Worst/best day + max peak-to-trough drawdown of the daily-cumulative equity."""
    if not daily:
        return 0.0, 0.0, 0.0
    cum, peak, mdd = 0.0, 0.0, 0.0
    for d in sorted(daily):
        cum += daily[d]
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    return min(daily.values()), max(daily.values()), mdd


def print_report(args, all_trades, simulated, degenerate, no_candle, bad_candle,
                 taken, dropped, halt_times):
    pc, ds = args.per_trade_cap, args.daily_stop
    A = [t.base_pnl for t in simulated]
    B = [t.cap_pnl for t in simulated]
    C = [t.cap_pnl for t in taken]
    sA, sB, sC = _stats(A), _stats(B), _stats(C)

    have_real = [t for t in simulated if t.real_pnl is not None]
    real_net = sum(t.real_pnl for t in have_real)
    a_sub = sum(t.base_pnl for t in have_real)   # re-sim over the SAME subset (apples-to-apples)

    span_days = len({t.entry_time.astimezone(IST).date() for t in simulated})
    d0 = min(t.entry_time.astimezone(IST).date() for t in simulated)
    d1 = max(t.entry_time.astimezone(IST).date() for t in simulated)

    print(f"\n{'='*94}")
    print(f"  S5 SHADOW — FILTERED + RISK-CAPPED SIMULATION")
    print(f"  setups={','.join(args.setups)}  ADR>={args.min_adr}  conf>={args.confidence:g}  "
          f"per-trade cap=Rs {pc:,.0f}/lot  daily stop=Rs {ds:,.0f} [{args.daily_mode}]")
    print(f"  {d0} to {d1}  ({span_days} trading days)")
    print(f"{'='*94}")
    print(f"  Population: {len(all_trades)} filtered  ->  {len(simulated)} simulated   "
          f"(dropped: {degenerate} degenerate geometry, {no_candle} no candles, "
          f"{bad_candle} corrupt candles)")
    print(f"  Shadow trades are 1 lot; P&L is on the real fill price + entry time, "
          f"re-simulated on 1m candles.")

    # Policy ladder
    print(f"\n  {'POLICY':<34} {'trades':>7} {'net P&L':>14} {'hit%':>6} "
          f"{'avg win':>10} {'avg loss':>10} {'PF':>6} {'exp/trd':>9}")
    print(f"  {'-'*34} {'-'*7} {'-'*14} {'-'*6} {'-'*10} {'-'*10} {'-'*6} {'-'*9}")
    for label, s in (
        ("A. baseline (structural exits)", sA),
        (f"B. + Rs{pc/1000:g}K/lot per-trade cap", sB),
        (f"C. + Rs{ds/1000:g}K daily stop", sC),
    ):
        pf = f"{s['pf']:.2f}" if s["pf"] != float("inf") else "inf"
        print(f"  {label:<34} {s['n']:>7} {s['net']:>+14,.0f} {s['hit']:>5.1f}% "
              f"{s['avg_w']:>+10,.0f} {s['avg_l']:>+10,.0f} {pf:>6} {s['exp']:>+9,.0f}")

    # Calibration anchor (re-sim vs real prod realized, on the SAME subset)
    print(f"\n  Engine-fidelity anchor (NO overlays, same {len(have_real)} trades with real P&L):")
    print(f"    real prod realized P&L : Rs {real_net:>+13,.0f}")
    if real_net:
        print(f"    re-sim baseline (A)    : Rs {a_sub:>+13,.0f}   "
              f"(ratio {a_sub/real_net:.2f}x — trust relative deltas, not magnitude)")

    # Is the directional skew real or a re-sim artifact? Split the REAL prod P&L too.
    print(f"\n  REAL prod P&L by cohort (regime check — confirms skew is in live fills, not the engine):")
    print(f"  {'cohort':<20} {'trades':>7} {'real net':>14} {'hit%':>6}")
    print(f"  {'-'*20} {'-'*7} {'-'*14} {'-'*6}")
    for key, fn in (("ORB", lambda t: t.setup == "ORB"),
                    ("PDH_PDL", lambda t: t.setup == "PDH_PDL"),
                    ("LONG", lambda t: t.is_long),
                    ("SHORT", lambda t: not t.is_long)):
        sub = [t.real_pnl for t in have_real if fn(t)]
        if sub:
            s = _stats(sub)
            print(f"  {key:<20} {s['n']:>7} {s['net']:>+14,.0f} {s['hit']:>5.1f}%")

    # Overlay effects
    cap_fires = [t for t in simulated if t.cap_bound]
    cap_delta = sB["net"] - sA["net"]
    print(f"\n  PER-TRADE CAP effect (A -> B):  net delta Rs {cap_delta:>+12,.0f}")
    print(f"    cap bound on {len(cap_fires)}/{len(simulated)} trades; "
          f"mean capped-trade loss Rs {mean([t.cap_pnl for t in cap_fires]):>+,.0f}"
          if cap_fires else "    cap never bound (structural stops were all tighter than the cap)")

    ds_delta = sC["net"] - sB["net"]
    dropped_pnl = sum(t.cap_pnl for t in dropped)
    print(f"\n  DAILY STOP effect (B -> C):  net delta Rs {ds_delta:>+12,.0f}")
    print(f"    days halted: {len(halt_times)}/{span_days}   trades dropped: {len(dropped)}")
    print(f"    P&L of dropped (would-be) trades: Rs {dropped_pnl:>+12,.0f}   "
          f"(daily stop is {'good — it cut net losers' if dropped_pnl < 0 else 'a drag — it cut net winners'})")
    if halt_times:
        print(f"    halted days: " + ", ".join(
            f"{d}({t.astimezone(IST).strftime('%H:%M')})" for d, t in sorted(halt_times.items())))

    # Per-setup / per-direction on the final policy C
    print(f"\n  Policy C by setup / direction:")
    print(f"  {'cohort':<20} {'trades':>7} {'net P&L':>14} {'hit%':>6}")
    print(f"  {'-'*20} {'-'*7} {'-'*14} {'-'*6}")
    for key, fn in (("ORB", lambda t: t.setup == "ORB"),
                    ("PDH_PDL", lambda t: t.setup == "PDH_PDL"),
                    ("LONG", lambda t: t.is_long),
                    ("SHORT", lambda t: not t.is_long)):
        sub = [t.cap_pnl for t in taken if fn(t)]
        if sub:
            s = _stats(sub)
            print(f"  {key:<20} {s['n']:>7} {s['net']:>+14,.0f} {s['hit']:>5.1f}%")

    # Risk curve
    for label, trs, attr in (("B (cap)", simulated, "cap_pnl"), ("C (cap+daily)", taken, "cap_pnl")):
        daily = _daily_pnl(trs, attr)
        worst, best, mdd = _curve_stats(daily)
        red = sum(1 for v in daily.values() if v < 0)
        print(f"\n  Policy {label}: worst day Rs {worst:>+11,.0f}   best day Rs {best:>+11,.0f}   "
              f"max drawdown Rs {mdd:>+11,.0f}   red days {red}/{len(daily)}")

    # Daily ledger
    print(f"\n  Daily ledger (capped pnl; '*'=halted, drops shown):")
    print(f"  {'date':<12} {'taken':>5} {'dropped':>7} {'B net':>13} {'C net':>13} {'halt':>7}")
    print(f"  {'-'*12} {'-'*5} {'-'*7} {'-'*13} {'-'*13} {'-'*7}")
    dropped_by_day = defaultdict(list)
    for t in dropped:
        dropped_by_day[t.entry_time.astimezone(IST).date()].append(t)
    taken_by_day = defaultdict(list)
    for t in taken:
        taken_by_day[t.entry_time.astimezone(IST).date()].append(t)
    alldays = sorted({t.entry_time.astimezone(IST).date() for t in simulated})
    for d in alldays:
        b_net = sum(t.cap_pnl for t in simulated if t.entry_time.astimezone(IST).date() == d)
        c_net = sum(t.cap_pnl for t in taken_by_day.get(d, []))
        nd = len(dropped_by_day.get(d, []))
        halt = halt_times.get(d)
        hl = halt.astimezone(IST).strftime("%H:%M") if halt else ""
        mark = "*" if halt else " "
        print(f"  {str(d):<12}{mark}{len(taken_by_day.get(d, [])):>4} {nd:>7} "
              f"{b_net:>+13,.0f} {c_net:>+13,.0f} {hl:>7}")

    print()


def print_faithful(args, fea, taken, dropped, halts):
    """Decision-grade ladder: the per-trade cap + daily stop applied to REAL outcomes."""
    pc, ds = args.per_trade_cap, args.daily_stop
    if not fea:
        return
    D = [t.real_pnl for t in fea]
    E = [t.faithful_pnl for t in fea]
    F = [t.faithful_pnl for t in taken]
    sD, sE, sF = _stats(D), _stats(E), _stats(F)
    span = len({t.entry_time.astimezone(IST).date() for t in fea})

    print(f"{'='*94}")
    print(f"  FAITHFUL OVERLAY — overlays applied to REAL prod outcomes  (decision-grade; "
          f"{len(fea)} closed trades)")
    print(f"  (real net_pnl kept; capped to -Rs{pc/1000:g}K only when the trade's real MAE "
          f"breached the floor)")
    print(f"{'='*94}")
    print(f"  {'POLICY':<34} {'trades':>7} {'net P&L':>14} {'hit%':>6} "
          f"{'avg win':>10} {'avg loss':>10} {'PF':>6} {'exp/trd':>9}")
    print(f"  {'-'*34} {'-'*7} {'-'*14} {'-'*6} {'-'*10} {'-'*10} {'-'*6} {'-'*9}")
    for label, s in (("D. real prod (no overlay)", sD),
                     (f"E. + Rs{pc/1000:g}K/lot per-trade cap", sE),
                     (f"F. + Rs{ds/1000:g}K daily stop", sF)):
        pf = f"{s['pf']:.2f}" if s["pf"] != float("inf") else "inf"
        print(f"  {label:<34} {s['n']:>7} {s['net']:>+14,.0f} {s['hit']:>5.1f}% "
              f"{s['avg_w']:>+10,.0f} {s['avg_l']:>+10,.0f} {pf:>6} {s['exp']:>+9,.0f}")

    cap_fires = [t for t in fea if t.faithful_cap_bound]
    cap_real_winners = sum(1 for t in cap_fires if t.real_pnl > 0)
    print(f"\n  CAP effect (D -> E):  net delta Rs {sE['net']-sD['net']:>+12,.0f}   "
          f"(real MAE breached -Rs{pc/1000:g}K on {len(cap_fires)}/{len(fea)} trades; "
          f"{cap_real_winners} of those were REAL WINNERS the cap chopped)")
    # Bracket the cap by trigger basis: WICK (any touch — aggressive) vs CLOSE (1m close —
    # a ~2s LTP poll misses brief wicks). The live ₹-stop sits between the two.
    real_total = sD["net"]
    for basis, attr in (("WICK (touch, upper-bound damage)", "mae_rupees"),
                        ("CLOSE (1m close, lower-bound)", "mae_close_rupees")):
        nb = sum(1 for t in fea if getattr(t, attr) <= -pc)
        net_b = sum((-pc if getattr(t, attr) <= -pc else t.real_pnl) for t in fea)
        print(f"      cap basis {basis:<34} bound {nb:>3}/{len(fea)}   "
              f"cap-only net Rs {net_b:>+11,.0f}   (delta {net_b-real_total:>+11,.0f})")
    dropped_pnl = sum(t.faithful_pnl for t in dropped)
    print(f"  DAILY STOP effect (E -> F):  net delta Rs {sF['net']-sE['net']:>+12,.0f}   "
          f"days halted {len(halts)}/{span}, dropped {len(dropped)} trades worth Rs {dropped_pnl:>+,.0f}")

    # Daily stop ISOLATED — on the REAL (uncapped) book, to separate the two overlays.
    d_taken, d_dropped, d_halts = apply_daily_stop(
        fea, ds, pnl_attr="real_pnl", exit_attr="real_exit_time")
    d_only = sum(t.real_pnl for t in d_taken)
    print(f"\n  Daily stop ALONE on the real book (no cap):  Rs {d_only:>+12,.0f}   "
          f"(vs Rs {sD['net']:>+,.0f} real)  — halted {len(d_halts)}/{span}, "
          f"dropped {len(d_dropped)} worth Rs {sum(t.real_pnl for t in d_dropped):>+,.0f}")

    print(f"\n  Policy F by direction (the regime signature):")
    for key, fn in (("LONG", lambda t: t.is_long), ("SHORT", lambda t: not t.is_long),
                    ("ORB", lambda t: t.setup == "ORB"), ("PDH_PDL", lambda t: t.setup == "PDH_PDL")):
        sub = [t.faithful_pnl for t in taken if fn(t)]
        if sub:
            s = _stats(sub)
            print(f"    {key:<9} {s['n']:>4} trades   net Rs {s['net']:>+12,.0f}   hit {s['hit']:>5.1f}%")

    daily = _daily_pnl(taken, "faithful_pnl")
    worst, best, mdd = _curve_stats(daily)
    print(f"\n  Policy F risk: worst day Rs {worst:>+11,.0f}   best day Rs {best:>+11,.0f}   "
          f"max drawdown Rs {mdd:>+11,.0f}   red days {sum(1 for v in daily.values() if v<0)}/{len(daily)}")

    # Per-trade cap LEVEL sweep (faithful, exact via stored MAE) — where does the cap stop
    # destroying the edge? Runs LAST: it overwrites faithful_pnl across cap levels.
    print(f"\n  PER-TRADE CAP LEVEL SWEEP (faithful, on real outcomes; E=cap only, F=cap+daily):")
    print(f"  {'cap/lot':>9} {'bound':>6} {'E: cap-only net':>16} {'F: +daily net':>15}")
    print(f"  {'-'*9} {'-'*6} {'-'*16} {'-'*15}")
    for cap in (6000, 8000, 10000, 12000, 15000, 20000, 30000, float("inf")):
        nb = 0
        for t in fea:
            b = t.mae_rupees <= -cap
            t.faithful_pnl = round(-cap if b else t.real_pnl, 2)
            nb += b
        e_net = sum(t.faithful_pnl for t in fea)
        # exit-time approx for the sweep: real exit (the cap-exit only shifts halts slightly)
        ft, _, _ = apply_daily_stop(fea, ds, pnl_attr="faithful_pnl", exit_attr="real_exit_time")
        f_net = sum(t.faithful_pnl for t in ft)
        cap_lbl = "none" if cap == float("inf") else f"{cap/1000:g}K"
        print(f"  {cap_lbl:>9} {nb:>6} {e_net:>+16,.0f} {f_net:>+15,.0f}")
    print()


# ── CLI ─────────────────────────────────────────────────────────────────

def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


async def run(args):
    from app.core.database import async_session_factory
    strat = await get_strategy_params("intraday_futures")
    be = strat.get("trailing_sl_breakeven_pct", 0.5)
    tr = strat.get("trailing_sl_trail_pct", 0.3)

    async with async_session_factory() as session:
        all_trades, degenerate = await fetch_filtered(
            session, args.setups, args.min_adr, args.confidence, args.start, args.end)
        if not all_trades:
            print("No trades match the filters.")
            return
        simulated, no_candle, bad_candle = await simulate(
            session, all_trades, args.per_trade_cap, be, tr)

    if not simulated:
        print("No simulated trades (all dropped for missing candles).")
        return

    taken, dropped, halt_times = apply_daily_stop(simulated, args.daily_stop)
    print_report(args, all_trades, simulated, degenerate, no_candle, bad_candle,
                 taken, dropped, halt_times)

    # Decision-grade pass: overlays on the REAL outcomes (engine only detects the floor).
    async with async_session_factory() as session:
        fea = await faithful_overlay(session, simulated, args.per_trade_cap)
    f_taken, f_dropped, f_halts = apply_daily_stop(
        fea, args.daily_stop, pnl_attr="faithful_pnl", exit_attr="faithful_exit_time")
    print_faithful(args, fea, f_taken, f_dropped, f_halts)

    if os.environ.get("DEBUG_CAPS"):
        cw = [t for t in fea if t.faithful_cap_bound and t.real_pnl > 0]
        print(f"\n  DEBUG — capped trades that were REAL WINNERS ({len(cw)}); the cap's cost:")
        print(f"  {'sym':<11} {'dir':<4} {'entry':>9} {'qty':>5} {'real P&L':>10} "
              f"{'MAE Rs':>9} {'MAE/sh':>8} {'MAE %':>7}")
        for t in sorted(cw, key=lambda x: x.real_pnl, reverse=True)[:12]:
            print(f"  {t.symbol:<11} {('LONG' if t.is_long else 'SHORT'):<4} "
                  f"{t.entry_price:>9.2f} {t.quantity:>5} {t.real_pnl:>+10,.0f} "
                  f"{t.mae_rupees:>+9,.0f} {t.mae_rupees/t.quantity:>+8.2f} "
                  f"{t.mae_rupees/t.quantity/t.entry_price*100:>+6.2f}%")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--setups", type=lambda s: [x for x in s.split(",") if x],
                   default=["ORB", "PDH_PDL"], help="Comma-separated setups (default ORB,PDH_PDL)")
    p.add_argument("--min-adr", type=float, default=2.8, help="Min adr_pct (default 2.8)")
    p.add_argument("--confidence", type=float, default=40, help="Min confidence (default 40)")
    p.add_argument("--per-trade-cap", type=float, default=8000,
                   help="Per-trade MTM loss cap in rupees per lot (default 8000)")
    p.add_argument("--daily-stop", type=float, default=20000,
                   help="Daily realized-loss breaker in rupees (default 20000)")
    p.add_argument("--daily-mode", choices=["halt", "flatten"], default="halt",
                   help="halt = stop new entries (open trades run out); flatten not yet wired")
    p.add_argument("--start", type=parse_date, default=date(2026, 4, 29))
    p.add_argument("--end", type=parse_date, default=date(2026, 6, 16))
    args = p.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
